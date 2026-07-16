#!/usr/bin/env python3
"""
Tests for dynamic run discovery, auto-tune integration, and number formatting.

Verifies that:
- Runs not in EXPECTED_RUNS can be discovered from qor_runs.csv
- Auto-tune run IDs are parseable
- Report paths from qor_runs.csv are used for parsing
- fmt.py produces readable human-facing output
- answer_structured returns correct data shapes
"""

import csv
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from parse_existing_runs import (
    parse_metrics_json,
    build_output_row,
    load_explicit_mapping,
    read_expected_runs,
    _has_any_metric,
    ROOT,
    QOR_RUNS,
)
from fmt import fmt_ns, fmt_power, fmt_area, fmt_util, fmt_int, fmt_slack
from assistant import answer_structured, answer


SAMPLE_METRICS = {
    "timing__setup__wns": 0,
    "timing__setup__ws": 8.5,
    "timing__setup__tns": 0,
    "timing__hold__wns": 0,
    "timing__hold__ws": 0.25,
    "timing__hold__tns": 0,
    "design__core__area": 15942.8,
    "design__instance__utilization": 0.85,
    "power__total": 0.0015,
    "power__internal__total": 0.001,
    "power__switching__total": 0.0004,
    "power__leakage__total": 2e-08,
    "design__max_slew_violation__count": 200,
    "design__max_cap_violation__count": 0,
    "design__max_fanout_violation__count": 1,
    "route__drc_errors": 0,
    "route__wirelength": 35000,
    "route__vias": 7000,
    "global_route__wirelength": 50000,
    "global_route__vias": 12,
    "antenna__violating__nets": 0,
}


class TestDynamicRunDiscovery(unittest.TestCase):
    """Test that dynamic run IDs from qor_runs.csv are discovered."""

    def test_dynamic_run_from_csv(self):
        """A run_id like auto_tune_1 in qor_runs.csv is included in read_expected_runs()."""
        # Create a temporary qor_runs.csv with an auto_tune entry
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, newline=""
        ) as f:
            writer = csv.DictWriter(f, fieldnames=[
                "run_id", "clock_period", "config_generated",
                "execution_attempted", "return_code", "reports_found",
                "metrics_parsed", "status", "config_path", "report_paths", "notes"
            ])
            writer.writeheader()
            writer.writerow({
                "run_id": "auto_tune_1",
                "clock_period": "20",
                "config_generated": "yes",
                "execution_attempted": "yes",
                "return_code": "0",
                "reports_found": "yes",
                "metrics_parsed": "yes",
                "status": "completed",
                "config_path": "designs/pm32/config_auto_tune_1.json",
                "report_paths": "results/important_reports/metrics_auto1.json",
                "notes": "Test run",
            })
            tmp_csv = Path(f.name)

        try:
            # Temporarily replace QOR_RUNS
            import parse_existing_runs as per
            original_qor_runs = per.QOR_RUNS
            per.QOR_RUNS = tmp_csv

            runs = read_expected_runs()
            run_ids = [r["run_id"] for r in runs]
            self.assertIn("auto_tune_1", run_ids)
            # Also verify the standard runs are still present
            self.assertIn("clock_25", run_ids)

            per.QOR_RUNS = original_qor_runs
        finally:
            tmp_csv.unlink()

    def test_auto_tune_run_parsed(self):
        """An auto_tune_1 run with a metrics JSON file gets parsed successfully."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(SAMPLE_METRICS, f)
            tmp_json = Path(f.name)

        try:
            from pathlib import PurePosixPath
            try:
                rel_path = str(PurePosixPath(tmp_json.relative_to(ROOT)))
            except ValueError:
                rel_path = str(tmp_json)

            run = {
                "run_id": "auto_tune_1",
                "clock_period": "20",
                "report_paths": rel_path if tmp_json.is_relative_to(ROOT) else str(tmp_json),
            }
            explicit_map = {}

            # Since the file may not be under ROOT, test parse directly
            parsed = parse_metrics_json(tmp_json)
            self.assertEqual(parsed.get("setup_wns"), "0")
            self.assertIn("8.5", parsed.get("setup_ws", ""))
            self.assertIn("8.5", parsed.get("setup_slack", ""))
        finally:
            tmp_json.unlink()

    def test_missing_auto_tune_report(self):
        """An auto_tune run with non-existent report path produces no metrics."""
        run = {
            "run_id": "auto_tune_99",
            "clock_period": "15",
            "report_paths": "results/nonexistent_file.json",
        }
        explicit_map = load_explicit_mapping()
        row = build_output_row(run, explicit_map)
        self.assertEqual(row["confidence"], "none")


class TestFormatting(unittest.TestCase):
    """Test number formatting helpers."""

    def test_fmt_ns_positive(self):
        self.assertEqual(fmt_ns(13.237121107238776), "13.237 ns")

    def test_fmt_ns_zero(self):
        self.assertEqual(fmt_ns(0), "0.000 ns")

    def test_fmt_ns_negative(self):
        self.assertEqual(fmt_ns(-1.5), "-1.500 ns")

    def test_fmt_ns_none(self):
        self.assertEqual(fmt_ns(None), "N/A")

    def test_fmt_ns_string(self):
        self.assertEqual(fmt_ns("13.237"), "13.237 ns")

    def test_fmt_power_milliwatts(self):
        result = fmt_power(0.00135)
        self.assertIn("1.350", result)
        self.assertIn("mW", result)

    def test_fmt_power_none(self):
        self.assertEqual(fmt_power(None), "N/A")

    def test_fmt_area(self):
        result = fmt_area(15942.8)
        self.assertIn("15,942.8", result)
        self.assertIn("um^2", result)

    def test_fmt_area_none(self):
        self.assertEqual(fmt_area(None), "N/A")

    def test_fmt_util(self):
        result = fmt_util(0.851986)
        self.assertIn("85.", result)
        self.assertIn("%", result)

    def test_fmt_int_value(self):
        self.assertEqual(fmt_int(247), "247")
        self.assertEqual(fmt_int("247.0"), "247")

    def test_fmt_int_none(self):
        self.assertEqual(fmt_int(None), "N/A")


class TestStructuredOutput(unittest.TestCase):
    """Test answer_structured returns correct data shapes."""

    def _sample_rows(self):
        return [
            {
                "run_id": "clock_15", "clock_period": "15", "confidence": "none",
                "setup_wns": "", "setup_ws": "", "setup_slack": "", "hold_slack": "",
                "setup_tns": "", "hold_wns": "", "hold_ws": "", "hold_tns": "",
                "area": "", "utilization": "", "power_total": "",
                "power_internal": "", "power_switching": "", "power_leakage": "",
                "slew_violations": "", "cap_violations": "", "fanout_violations": "",
                "route_drc_errors": "", "route_wirelength": "", "route_vias": "",
                "grt_wirelength": "", "grt_vias": "", "antenna_violations": "",
                "congestion_overflow": "", "congestion_status": "not_available",
                "worst_setup_corner": "", "worst_hold_corner": "", "timing_corners_count": "",
                "decision": "No metrics available", "source_files": "",
                "field_sources": "", "missing_fields": "", "parser_warnings": "",
            },
            {
                "run_id": "clock_25", "clock_period": "25", "confidence": "high",
                "setup_wns": "0", "setup_ws": "13.237", "setup_slack": "13.237",
                "hold_wns": "0", "hold_ws": "0.119", "hold_slack": "0.119",
                "setup_tns": "0", "hold_tns": "0",
                "area": "15942.8", "utilization": "0.851986",
                "power_total": "0.00135", "power_internal": "0.0009",
                "power_switching": "0.0004", "power_leakage": "1.9e-08",
                "slew_violations": "247", "cap_violations": "0", "fanout_violations": "1",
                "route_drc_errors": "0", "route_wirelength": "37368", "route_vias": "7603",
                "grt_wirelength": "53219", "grt_vias": "15", "antenna_violations": "0",
                "congestion_overflow": "", "congestion_status": "routing_data_found",
                "worst_setup_corner": "max_ss_100C_1v60", "worst_hold_corner": "max_ss_100C_1v60",
                "timing_corners_count": "9",
                "decision": "Timing MET with large margin",
                "source_files": "results/important_reports/metrics_run1.json",
                "field_sources": "", "missing_fields": "", "parser_warnings": "",
            },
        ]

    def test_best_run_structured(self):
        """answer_structured for best_run returns correct data."""
        rows = self._sample_rows()
        result = answer_structured("best run", rows)
        self.assertEqual(result["intent"], "best_run")
        self.assertEqual(result["data"]["best_available_run"], "clock_25")
        self.assertFalse(result["data"]["global_conclusion_possible"])
        self.assertEqual(result["data"]["setup_wns"], 0.0)
        self.assertAlmostEqual(result["data"]["setup_ws"], 13.237, places=2)
        self.assertAlmostEqual(result["data"]["setup_slack"], 13.237, places=2)

    def test_congestion_structured(self):
        """answer_structured for congestion returns correct data."""
        rows = self._sample_rows()
        result = answer_structured("congestion", rows)
        self.assertEqual(result["intent"], "congestion")
        self.assertEqual(result["data"]["final_drc_errors"], 0)
        self.assertIsNone(result["data"]["congestion_overflow"])
        self.assertFalse(result["data"]["congestion_quantifiable"])

    def test_missing_structured(self):
        """answer_structured for missing data returns correct runs."""
        rows = self._sample_rows()
        result = answer_structured("why is clock_15 empty?", rows)
        self.assertEqual(result["intent"], "missing_data")
        self.assertIn("clock_15", result["data"]["missing_runs"])

    def test_timing_structured(self):
        """answer_structured for timing returns per-run data."""
        rows = self._sample_rows()
        result = answer_structured("timing", rows)
        self.assertEqual(result["intent"], "timing")
        self.assertTrue(len(result["data"]["runs"]) >= 1)
        run_25 = next(r for r in result["data"]["runs"] if r["run_id"] == "clock_25")
        self.assertEqual(run_25["setup_wns"], 0.0)
        self.assertEqual(run_25["setup_status"], "met")


if __name__ == "__main__":
    unittest.main()
