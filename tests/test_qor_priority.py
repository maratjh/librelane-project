#!/usr/bin/env python3
"""
Tests for recommendation priority, QoR classification, Pareto analysis,
and LLM numerical validation.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from rule_engine import (
    recommend_action_structured,
    recommend_action,
    classify_qor,
    qor_status,
    best_run_selection,
    select_fastest_setup_clean_run,
)
from llm_client import validate_llm_response, _build_allowlist


def _row(**kw):
    """Build a row with sensible defaults."""
    defaults = {
        "run_id": "test_run", "clock_period": "25", "confidence": "high",
        "setup_wns": "0", "setup_ws": "5.0", "setup_tns": "0",
        "setup_slack": "5.0",
        "hold_wns": "0", "hold_ws": "0.5", "hold_tns": "0",
        "hold_slack": "0.5",
        "area": "15000", "utilization": "0.70",
        "power_total": "0.001", "power_internal": "0.0006",
        "power_switching": "0.0003", "power_leakage": "1e-08",
        "slew_violations": "0", "cap_violations": "0", "fanout_violations": "0",
        "route_drc_errors": "0", "route_wirelength": "30000",
        "route_vias": "5000", "grt_wirelength": "40000", "grt_vias": "10",
        "antenna_violations": "0", "congestion_overflow": "",
        "congestion_status": "routing_data_found",
        "worst_setup_corner": "", "worst_hold_corner": "",
        "timing_corners_count": "", "decision": "",
        "source_files": "test.json", "field_sources": "",
        "missing_fields": "", "parser_warnings": "",
    }
    defaults.update(kw)
    return defaults


class TestRecommendationPriority(unittest.TestCase):
    """Test that blocking issues prevent clock optimization."""

    def test_drc_failure_blocks_clock_optimization(self):
        row = _row(setup_slack="10.0", route_drc_errors="3")
        r = recommend_action_structured(row)
        self.assertIn("route_drc_errors", r["blocking_issues"])
        self.assertNotEqual(r["primary_category"], "clock_optimization")

    def test_hold_failure_blocks_clock_optimization(self):
        row = _row(setup_slack="10.0", hold_slack="-0.3")
        r = recommend_action_structured(row)
        self.assertIn("hold_timing_failure", r["blocking_issues"])
        self.assertNotEqual(r["primary_category"], "clock_optimization")

    def test_setup_failure_blocks_clock_optimization(self):
        row = _row(setup_slack="-2.0", setup_wns="-2.0")
        r = recommend_action_structured(row)
        self.assertIn("setup_timing_failure", r["blocking_issues"])
        self.assertEqual(r["primary_category"], "setup_timing_failure")

    def test_slew_violations_block_clock_optimization(self):
        row = _row(setup_slack="13.0", slew_violations="247")
        r = recommend_action_structured(row)
        self.assertIn("slew_violations", r["blocking_issues"])
        self.assertEqual(r["primary_category"], "electrical_violations")

    def test_cap_violations_block_clock_optimization(self):
        row = _row(setup_slack="8.0", cap_violations="5")
        r = recommend_action_structured(row)
        self.assertIn("cap_violations", r["blocking_issues"])

    def test_fanout_violations_block_clock_optimization(self):
        row = _row(setup_slack="8.0", fanout_violations="2")
        r = recommend_action_structured(row)
        self.assertIn("fanout_violations", r["blocking_issues"])

    def test_congestion_overflow_blocks_clock_optimization(self):
        row = _row(setup_slack="8.0", congestion_overflow="15.0")
        r = recommend_action_structured(row)
        self.assertIn("congestion_overflow", r["blocking_issues"])

    def test_high_utilization_warns_before_clock_optimization(self):
        row = _row(setup_slack="8.0", utilization="0.92")
        r = recommend_action_structured(row)
        self.assertIn("very_high_utilization", r["blocking_issues"])

    def test_clean_large_margin_allows_clock_reduction(self):
        row = _row(setup_slack="8.0", slew_violations="0", cap_violations="0",
                   fanout_violations="0", route_drc_errors="0", utilization="0.60")
        r = recommend_action_structured(row)
        self.assertEqual(r["primary_category"], "clock_optimization")
        self.assertEqual(len(r["blocking_issues"]), 0)

    def test_backward_compat_string_api(self):
        row = _row(setup_slack="8.0")
        text = recommend_action(row)
        self.assertIsInstance(text, str)
        self.assertTrue(len(text) > 10)

    # --- Combined-blocker priority tests ---

    def test_drc_plus_hold_plus_setup_reports_drc_first(self):
        """When DRC, hold, and setup all fail, DRC has highest priority."""
        row = _row(setup_slack="-2.0", setup_wns="-2.0",
                   hold_slack="-0.5", route_drc_errors="5")
        r = recommend_action_structured(row)
        self.assertEqual(r["primary_category"], "route_drc_errors")
        self.assertEqual(r["priority"], 2)
        self.assertIn("route_drc_errors", r["blocking_issues"])
        self.assertIn("hold_timing_failure", r["blocking_issues"])
        self.assertIn("setup_timing_failure", r["blocking_issues"])

    def test_hold_plus_setup_reports_hold_first(self):
        """When hold and setup both fail, hold has higher priority."""
        row = _row(setup_slack="-1.0", setup_wns="-1.0",
                   hold_slack="-0.2", route_drc_errors="0")
        r = recommend_action_structured(row)
        self.assertEqual(r["primary_category"], "hold_timing_failure")
        self.assertEqual(r["priority"], 3)
        self.assertIn("hold_timing_failure", r["blocking_issues"])
        self.assertIn("setup_timing_failure", r["blocking_issues"])

    def test_setup_plus_slew_reports_setup_first(self):
        """When setup fails and slew violations exist, setup has higher priority."""
        row = _row(setup_slack="-3.0", setup_wns="-3.0", slew_violations="100")
        r = recommend_action_structured(row)
        self.assertEqual(r["primary_category"], "setup_timing_failure")
        self.assertEqual(r["priority"], 4)
        self.assertIn("setup_timing_failure", r["blocking_issues"])
        self.assertIn("slew_violations", r["blocking_issues"])

    def test_drc_plus_congestion_reports_drc_first(self):
        """When DRC and congestion both present, DRC has higher priority."""
        row = _row(setup_slack="5.0", route_drc_errors="2", congestion_overflow="10.0")
        r = recommend_action_structured(row)
        self.assertEqual(r["primary_category"], "route_drc_errors")
        self.assertEqual(r["priority"], 2)
        self.assertIn("route_drc_errors", r["blocking_issues"])
        self.assertIn("congestion_overflow", r["blocking_issues"])

    def test_slew_plus_fanout_plus_utilization(self):
        """Multiple electrical + utilization issues → electrical_violations category."""
        row = _row(setup_slack="8.0", slew_violations="50",
                   fanout_violations="3", utilization="0.92")
        r = recommend_action_structured(row)
        self.assertEqual(r["primary_category"], "electrical_violations")
        self.assertIn("slew_violations", r["blocking_issues"])
        self.assertIn("fanout_violations", r["blocking_issues"])
        self.assertIn("very_high_utilization", r["blocking_issues"])


class TestQoRClassification(unittest.TestCase):
    """Test classify_qor and qor_status."""

    def test_fastest_setup_clean_run(self):
        rows = [
            _row(run_id="fast", clock_period="15", setup_slack="2.0"),
            _row(run_id="slow", clock_period="30", setup_slack="10.0"),
        ]
        _, _, s = select_fastest_setup_clean_run(rows)
        self.assertEqual(s["best_available_run"], "fast")

    def test_fastest_setup_clean_not_equal_best_qor(self):
        rows = [
            _row(run_id="fast_dirty", clock_period="15", setup_slack="2.0", slew_violations="100"),
            _row(run_id="slow_clean", clock_period="30", setup_slack="8.0"),
        ]
        _, _, s = select_fastest_setup_clean_run(rows)
        self.assertEqual(s["best_available_run"], "fast_dirty")
        cls = classify_qor(rows[0])
        self.assertFalse(cls["qor_clean"])

    def test_no_qor_clean_run(self):
        rows = [
            _row(run_id="a", setup_slack="5.0", slew_violations="10"),
            _row(run_id="b", setup_slack="8.0", fanout_violations="2"),
        ]
        status = qor_status(rows)
        self.assertEqual(status["qor_clean_runs"], [])

    def test_qor_clean_run_selection(self):
        rows = [_row(run_id="clean", setup_slack="3.0")]
        status = qor_status(rows)
        self.assertIn("clean", status["qor_clean_runs"])

    def test_pareto_candidates(self):
        rows = [
            _row(run_id="a", clock_period="20", setup_slack="3.0", power_total="0.002"),
            _row(run_id="b", clock_period="25", setup_slack="5.0", power_total="0.001"),
        ]
        status = qor_status(rows)
        # Both should be Pareto (a is faster, b has less power)
        self.assertIn("a", status["pareto_candidates"])
        self.assertIn("b", status["pareto_candidates"])

    def test_missing_metrics_prevent_global_conclusion(self):
        rows = [
            _row(run_id="avail", setup_slack="5.0"),
            _row(run_id="missing", confidence="none", setup_slack=""),
        ]
        status = qor_status(rows)
        self.assertFalse(status["global_conclusion_possible"])

    def test_dynamic_run_names(self):
        rows = [_row(run_id="auto_tune_7", clock_period="18", setup_slack="2.0")]
        _, _, s = select_fastest_setup_clean_run(rows)
        self.assertEqual(s["best_available_run"], "auto_tune_7")


class TestLLMNumericalValidation(unittest.TestCase):
    """Test LLM post-response numerical validator."""

    def _rows(self):
        return [_row(setup_ws="13.237", setup_slack="13.237", power_total="0.00135")]

    def test_supported_exact_value(self):
        response = "The setup slack is 13.237 ns."
        v = validate_llm_response(response, self._rows(), [], "What is slack?")
        self.assertTrue(v["valid"])

    def test_supported_rounded_value(self):
        response = "Power is approximately 1.350 mW."
        v = validate_llm_response(response, self._rows(), [], "power?")
        self.assertTrue(v["valid"])

    def test_unsupported_fabricated_wns(self):
        response = "The WNS is -3.5 ns, indicating timing failure."
        v = validate_llm_response(response, self._rows(), [], "timing?")
        self.assertFalse(v["valid"])
        self.assertIn("3.5", v["unsupported_numbers"])

    def test_unsupported_congestion_percentage(self):
        response = "Congestion overflow is 12.7% which is moderate."
        v = validate_llm_response(response, self._rows(), [], "congestion?")
        self.assertFalse(v["valid"])

    def test_unsupported_power_value(self):
        response = "The total power consumption is 5.2 W."
        v = validate_llm_response(response, self._rows(), [], "power?")
        self.assertFalse(v["valid"])

    def test_text_only_explanation(self):
        response = "The timing is met. No violations are blocking."
        v = validate_llm_response(response, self._rows(), [], "timing?")
        self.assertTrue(v["valid"])

    def test_empty_response(self):
        v = validate_llm_response("", self._rows(), [], "test?")
        self.assertTrue(v["valid"])


if __name__ == "__main__":
    unittest.main()
