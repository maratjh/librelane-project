#!/usr/bin/env python3
"""
Unit tests for scripts/rule_engine.py

Tests rule-based QoR diagnosis: threshold logic, severity assignments,
explanation text, recommendations, and best-run selection.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from rule_engine import (
    diagnose_run,
    recommend_action,
    best_run_selection,
    compare_runs,
    _explain_setup,
    _explain_congestion,
    _get_setup_slack,
    LARGE_SETUP_MARGIN_NS,
    CRITICAL_SETUP_MARGIN_NS,
    HIGH_UTILIZATION_THRESHOLD,
    VERY_HIGH_UTILIZATION_THRESHOLD,
)


def _make_row(**kwargs):
    """Helper to build a row dict with defaults."""
    defaults = {
        "run_id": "clock_25",
        "design": "pm32",
        "clock_period": "25",
        "setup_wns": "",
        "setup_ws": "",
        "setup_tns": "",
        "hold_wns": "",
        "hold_ws": "",
        "hold_tns": "",
        "setup_slack": "",
        "hold_slack": "",
        "area": "",
        "utilization": "",
        "power_total": "",
        "power_internal": "",
        "power_switching": "",
        "power_leakage": "",
        "slew_violations": "",
        "cap_violations": "",
        "fanout_violations": "",
        "route_drc_errors": "",
        "route_wirelength": "",
        "route_vias": "",
        "grt_wirelength": "",
        "grt_vias": "",
        "antenna_violations": "",
        "congestion_overflow": "",
        "congestion_status": "",
        "worst_setup_corner": "",
        "worst_hold_corner": "",
        "timing_corners_count": "",
        "decision": "",
        "source_files": "",
        "field_sources": "",
        "missing_fields": "",
        "confidence": "high",
        "parser_warnings": "",
    }
    defaults.update(kwargs)
    return defaults


class TestSetupDiagnosis(unittest.TestCase):
    """Tests for setup timing diagnosis severity levels."""

    def test_setup_failure(self):
        """setup_slack < 0 → severity=high."""
        row = _make_row(setup_slack="-1.5", setup_wns="-1.5", setup_ws="-1.5")
        diags = diagnose_run(row)
        setup_diags = [d for d in diags if d["category"] == "setup_timing"]
        self.assertEqual(len(setup_diags), 1)
        self.assertEqual(setup_diags[0]["severity"], "high")
        self.assertIn("fails", setup_diags[0]["finding"].lower())

    def test_setup_small_margin(self):
        """0 < slack < 0.5 → severity=warning."""
        row = _make_row(setup_slack="0.3", setup_wns="0", setup_ws="0.3")
        diags = diagnose_run(row)
        setup_diags = [d for d in diags if d["category"] == "setup_timing"]
        self.assertEqual(len(setup_diags), 1)
        self.assertEqual(setup_diags[0]["severity"], "warning")
        self.assertIn("small margin", setup_diags[0]["finding"].lower())

    def test_setup_reasonable_margin(self):
        """0.5 <= slack <= 5 → severity=ok."""
        row = _make_row(setup_slack="2.5", setup_wns="0", setup_ws="2.5")
        diags = diagnose_run(row)
        setup_diags = [d for d in diags if d["category"] == "setup_timing"]
        self.assertEqual(len(setup_diags), 1)
        self.assertEqual(setup_diags[0]["severity"], "ok")
        self.assertIn("reasonable", setup_diags[0]["finding"].lower())

    def test_setup_large_margin(self):
        """slack > 5 → severity=info."""
        row = _make_row(setup_slack="13.237", setup_wns="0", setup_ws="13.237")
        diags = diagnose_run(row)
        setup_diags = [d for d in diags if d["category"] == "setup_timing"]
        self.assertEqual(len(setup_diags), 1)
        self.assertEqual(setup_diags[0]["severity"], "info")
        self.assertIn("large margin", setup_diags[0]["finding"].lower())


class TestHoldDiagnosis(unittest.TestCase):
    """Tests for hold timing diagnosis."""

    def test_hold_failure(self):
        """hold_slack < 0 → severity=high."""
        row = _make_row(hold_slack="-0.2", hold_wns="-0.2")
        diags = diagnose_run(row)
        hold_diags = [d for d in diags if d["category"] == "hold_timing"]
        self.assertEqual(len(hold_diags), 1)
        self.assertEqual(hold_diags[0]["severity"], "high")
        self.assertIn("fails", hold_diags[0]["finding"].lower())

    def test_hold_met(self):
        """hold_slack >= 0 → severity=ok."""
        row = _make_row(hold_slack="0.119", hold_wns="0", hold_ws="0.119")
        diags = diagnose_run(row)
        hold_diags = [d for d in diags if d["category"] == "hold_timing"]
        self.assertEqual(len(hold_diags), 1)
        self.assertEqual(hold_diags[0]["severity"], "ok")
        self.assertIn("met", hold_diags[0]["finding"].lower())


class TestUtilizationDiagnosis(unittest.TestCase):
    """Tests for utilization-based diagnosis."""

    def test_high_utilization(self):
        """util > 0.85 → warning about congestion risk."""
        row = _make_row(utilization="0.90")
        diags = diagnose_run(row)
        util_diags = [d for d in diags if d["category"] == "utilization"]
        self.assertEqual(len(util_diags), 1)
        self.assertEqual(util_diags[0]["severity"], "warning")
        self.assertIn("very high", util_diags[0]["finding"].lower())


class TestCongestionExplanation(unittest.TestCase):
    """Tests for _explain_congestion logic."""

    def test_zero_drc_no_congestion_proof(self):
        """DRC=0, no overflow → message says does NOT prove zero congestion."""
        row = _make_row(
            route_drc_errors="0",
            congestion_status="routing_data_found",
            congestion_overflow="",
        )
        explanation = _explain_congestion(row)
        self.assertIn("does NOT prove", explanation)

    def test_explicit_congestion_overflow(self):
        """Overflow field present → reported in explanation."""
        row = _make_row(
            congestion_overflow="15.3",
            congestion_status="overflow_data_available",
        )
        explanation = _explain_congestion(row)
        self.assertIn("15.3", explanation)


class TestMissingMetrics(unittest.TestCase):
    """Tests for handling of missing data."""

    def test_missing_metrics(self):
        """All empty fields → appropriate empty diagnosis list."""
        row = _make_row(confidence="none")
        # All metric fields are empty, so diagnose_run returns empty or minimal
        diags = diagnose_run(row)
        # No setup/hold diag since slack is empty
        setup_diags = [d for d in diags if d["category"] == "setup_timing"]
        hold_diags = [d for d in diags if d["category"] == "hold_timing"]
        self.assertEqual(len(setup_diags), 0)
        self.assertEqual(len(hold_diags), 0)


class TestBestRunSelection(unittest.TestCase):
    """Tests for best_run_selection logic."""

    def test_best_run_with_missing_faster(self):
        """clock_15-22 missing, clock_25 passing → best=clock_25, global_conclusion_possible=False."""
        rows = [
            _make_row(run_id="clock_15", clock_period="15", confidence="none"),
            _make_row(run_id="clock_18", clock_period="18", confidence="none"),
            _make_row(run_id="clock_20", clock_period="20", confidence="none"),
            _make_row(run_id="clock_22", clock_period="22", confidence="none"),
            _make_row(
                run_id="clock_25", clock_period="25",
                setup_slack="13.237", setup_wns="0", setup_ws="13.237",
                confidence="high",
            ),
            _make_row(
                run_id="clock_30", clock_period="30",
                setup_slack="18.0", setup_wns="0", setup_ws="18.0",
                confidence="high",
            ),
        ]
        best_row, explanation, structured = best_run_selection(rows)
        self.assertIsNotNone(best_row)
        self.assertEqual(best_row["run_id"], "clock_25")
        self.assertFalse(structured["global_conclusion_possible"])
        self.assertEqual(structured["missing_run_count"], 4)

    def test_no_timing_clean_run(self):
        """All runs fail → appropriate message."""
        rows = [
            _make_row(
                run_id="clock_25", clock_period="25",
                setup_slack="-1.5", setup_wns="-1.5",
                confidence="high",
            ),
            _make_row(
                run_id="clock_30", clock_period="30",
                setup_slack="-0.5", setup_wns="-0.5",
                confidence="high",
            ),
        ]
        best_row, explanation, structured = best_run_selection(rows)
        self.assertIsNone(best_row)
        self.assertIn("No timing-clean run", explanation)


class TestExplainSetupTerminology(unittest.TestCase):
    """Tests for correct WNS/WS terminology in explanations."""

    def test_explain_setup_correct_terminology(self):
        """WNS=0, WS=13.237 → says 'OpenROAD setup WNS = 0' and 'Worst setup slack (margin) = 13.237'."""
        row = _make_row(
            setup_wns="0", setup_ws="13.237121107238776",
            setup_slack="13.237121107238776",
        )
        explanation = _explain_setup(row)
        self.assertIn("OpenROAD setup WNS = 0", explanation)
        self.assertIn("Worst setup slack (margin) = 13.237", explanation)
        self.assertIn("MET", explanation)


class TestRecommendAction(unittest.TestCase):
    """Tests for recommend_action logic."""

    def test_recommend_action_large_margin(self):
        """slack > 5 → 'Try a smaller CLOCK_PERIOD'."""
        row = _make_row(setup_slack="13.237", setup_wns="0", setup_ws="13.237")
        rec = recommend_action(row)
        self.assertIn("smaller CLOCK_PERIOD", rec)

    def test_recommend_action_failure(self):
        """slack < 0 → 'Increase CLOCK_PERIOD'."""
        row = _make_row(setup_slack="-1.5", setup_wns="-1.5")
        rec = recommend_action(row)
        self.assertIn("Increase CLOCK_PERIOD", rec)


class TestCompareRuns(unittest.TestCase):
    """Tests for compare_runs output."""

    def test_compare_two_runs(self):
        """Compare two runs → output mentions both run IDs."""
        rows = [
            _make_row(
                run_id="clock_25", clock_period="25",
                setup_slack="13.237", hold_slack="0.119",
                area="15942.8", slew_violations="247",
            ),
            _make_row(
                run_id="clock_30", clock_period="30",
                setup_slack="18.0", hold_slack="0.5",
                area="15942.8", slew_violations="200",
            ),
        ]
        result = compare_runs(rows, ["clock_25", "clock_30"])
        self.assertIn("clock_25", result)
        self.assertIn("clock_30", result)


if __name__ == "__main__":
    unittest.main()
