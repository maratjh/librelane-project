#!/usr/bin/env python3
"""
Integration tests for the LibreLane QoR debugging assistant.

Tests end-to-end flows: parsing → loading → querying,
schema completeness, path safety, and slack computation consistency.
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
    compute_setup_slack,
    FIELDNAMES,
)
from assistant import answer


SAMPLE_METRICS_JSON = {
    "timing__setup__wns": 0,
    "timing__setup__ws": 13.237121107238776,
    "timing__setup__tns": 0,
    "timing__hold__wns": 0,
    "timing__hold__ws": 0.11902446032334803,
    "timing__hold__tns": 0,
    "design__core__area": 15942.8,
    "design__instance__utilization": 0.851986,
    "power__total": 0.0013482653303071856,
    "power__internal__total": 0.0009364390862174332,
    "power__switching__total": 0.0004118068900424987,
    "power__leakage__total": 1.931543103239619e-08,
    "design__max_slew_violation__count": 247,
    "design__max_cap_violation__count": 0,
    "design__max_fanout_violation__count": 1,
    "route__drc_errors": 0,
    "route__wirelength": 37368,
    "route__vias": 7603,
    "global_route__wirelength": 53219,
    "global_route__vias": 15,
    "antenna__violating__nets": 0,
}


class TestEndToEndParseAndQuery(unittest.TestCase):
    """End-to-end: create temp JSON → parse → build row → query assistant."""

    def test_end_to_end_parse_and_query(self):
        """Parse a metrics JSON, build a row, query the assistant."""
        # Step 1: Create temp metrics JSON
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(SAMPLE_METRICS_JSON, f)
            f.flush()
            json_path = Path(f.name)

        try:
            # Step 2: Parse the JSON
            parsed = parse_metrics_json(json_path)
            self.assertIn("setup_wns", parsed)
            self.assertEqual(parsed["setup_wns"], "0")

            # Step 3: Build a row as the assistant would see it
            row = {
                "run_id": "clock_25",
                "design": "pm32",
                "clock_period": "25",
                "setup_wns": parsed.get("setup_wns", ""),
                "setup_ws": parsed.get("setup_ws", ""),
                "setup_tns": parsed.get("setup_tns", ""),
                "hold_wns": parsed.get("hold_wns", ""),
                "hold_ws": parsed.get("hold_ws", ""),
                "hold_tns": parsed.get("hold_tns", ""),
                "setup_slack": parsed.get("setup_slack", ""),
                "hold_slack": parsed.get("hold_slack", ""),
                "area": parsed.get("area", ""),
                "utilization": parsed.get("utilization", ""),
                "power_total": parsed.get("power_total", ""),
                "power_internal": parsed.get("power_internal", ""),
                "power_switching": parsed.get("power_switching", ""),
                "power_leakage": parsed.get("power_leakage", ""),
                "slew_violations": parsed.get("slew_violations", ""),
                "cap_violations": parsed.get("cap_violations", ""),
                "fanout_violations": parsed.get("fanout_violations", ""),
                "route_drc_errors": parsed.get("route_drc_errors", ""),
                "route_wirelength": parsed.get("route_wirelength", ""),
                "route_vias": parsed.get("route_vias", ""),
                "grt_wirelength": parsed.get("grt_wirelength", ""),
                "grt_vias": parsed.get("grt_vias", ""),
                "antenna_violations": parsed.get("antenna_violations", ""),
                "congestion_overflow": parsed.get("congestion_overflow", ""),
                "congestion_status": parsed.get("congestion_status", ""),
                "worst_setup_corner": parsed.get("worst_setup_corner", ""),
                "worst_hold_corner": parsed.get("worst_hold_corner", ""),
                "timing_corners_count": parsed.get("timing_corners_count", ""),
                "decision": "Timing MET with large margin",
                "source_files": str(json_path),
                "field_sources": "",
                "missing_fields": "",
                "confidence": "high",
                "parser_warnings": "",
            }

            # Step 4: Query the assistant
            result = answer("summary", [row])
            self.assertIn("run", result.lower())
            self.assertIn("25", result)

            # Step 5: Query best run
            result = answer("best run", [row])
            self.assertIn("clock_25", result)

        finally:
            json_path.unlink()


class TestNoAbsolutePathsInOutput(unittest.TestCase):
    """Verify parser output doesn't leak absolute paths."""

    def test_no_absolute_paths_in_output(self):
        """Parse metrics and check no C:\\ or /home/ in standard output fields."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(SAMPLE_METRICS_JSON, f)
            f.flush()
            json_path = Path(f.name)

        try:
            parsed = parse_metrics_json(json_path)
            # Check that parsed values don't contain absolute paths
            for key, value in parsed.items():
                if key.startswith("_"):
                    continue
                if isinstance(value, str):
                    self.assertNotIn("C:\\", value,
                                     f"Field {key} contains absolute Windows path")
                    self.assertNotIn("/home/", value,
                                     f"Field {key} contains absolute Linux path")
        finally:
            json_path.unlink()


class TestSchemaCompleteness(unittest.TestCase):
    """Verify CSV schema has all expected columns."""

    def test_schema_completeness(self):
        """FIELDNAMES contains all expected columns."""
        expected_columns = [
            "run_id", "design", "clock_period",
            "setup_wns", "setup_ws", "setup_tns",
            "hold_wns", "hold_ws", "hold_tns",
            "setup_slack", "hold_slack",
            "area", "utilization",
            "power_total", "power_internal", "power_switching", "power_leakage",
            "slew_violations", "cap_violations", "fanout_violations",
            "route_drc_errors", "route_wirelength", "route_vias",
            "grt_wirelength", "grt_vias", "antenna_violations",
            "congestion_overflow", "congestion_status",
            "worst_setup_corner", "worst_hold_corner", "timing_corners_count",
            "decision", "source_files", "field_sources",
            "missing_fields", "confidence", "parser_warnings",
        ]
        for col in expected_columns:
            self.assertIn(col, FIELDNAMES,
                          f"Expected column '{col}' missing from FIELDNAMES")


class TestSetupSlackComputedCorrectly(unittest.TestCase):
    """Verify setup_slack computation consistency."""

    def test_setup_slack_computed_correctly(self):
        """When WNS=0, setup_slack should equal WS."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(SAMPLE_METRICS_JSON, f)
            f.flush()
            json_path = Path(f.name)

        try:
            parsed = parse_metrics_json(json_path)
            # WNS = 0, so setup_slack should be WS
            setup_slack = float(parsed["setup_slack"])
            setup_ws = float(parsed["setup_ws"])
            self.assertAlmostEqual(setup_slack, setup_ws, places=6)

            # Also verify using the standalone function
            manual_slack = compute_setup_slack(0, 13.237121107238776)
            self.assertAlmostEqual(manual_slack, 13.237121107238776)
        finally:
            json_path.unlink()


class TestCsvRoundTrip(unittest.TestCase):
    """Test that metrics can be written to CSV and read back."""

    def test_csv_write_read_roundtrip(self):
        """Write a row to CSV, read it back, verify key fields."""
        row = {
            "run_id": "clock_25",
            "design": "pm32",
            "clock_period": "25",
            "setup_wns": "0",
            "setup_ws": "13.237121107238776",
            "setup_tns": "0",
            "hold_wns": "0",
            "hold_ws": "0.11902446032334803",
            "hold_tns": "0",
            "setup_slack": "13.237121107238776",
            "hold_slack": "0.11902446032334803",
            "area": "15942.8",
            "utilization": "0.851986",
            "power_total": "0.0013482653303071856",
            "power_internal": "0.0009364390862174332",
            "power_switching": "0.0004118068900424987",
            "power_leakage": "1.931543103239619e-08",
            "slew_violations": "247",
            "cap_violations": "0",
            "fanout_violations": "1",
            "route_drc_errors": "0",
            "route_wirelength": "37368",
            "route_vias": "7603",
            "grt_wirelength": "53219",
            "grt_vias": "15",
            "antenna_violations": "0",
            "congestion_overflow": "",
            "congestion_status": "routing_data_found",
            "worst_setup_corner": "nom_ss_100C_1v60",
            "worst_hold_corner": "nom_ss_100C_1v60",
            "timing_corners_count": "1",
            "decision": "Timing MET with large margin",
            "source_files": "results/metrics.json",
            "field_sources": "",
            "missing_fields": "",
            "confidence": "high",
            "parser_warnings": "",
        }

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, newline=""
        ) as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
            writer.writerow(row)
            csv_path = Path(f.name)

        try:
            with csv_path.open(newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                read_rows = list(reader)

            self.assertEqual(len(read_rows), 1)
            read_row = read_rows[0]
            self.assertEqual(read_row["run_id"], "clock_25")
            self.assertEqual(read_row["setup_wns"], "0")
            self.assertEqual(read_row["setup_ws"], "13.237121107238776")
            self.assertEqual(read_row["setup_slack"], "13.237121107238776")
            self.assertEqual(read_row["confidence"], "high")
        finally:
            csv_path.unlink()


if __name__ == "__main__":
    unittest.main()
