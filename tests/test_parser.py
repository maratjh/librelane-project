#!/usr/bin/env python3
"""
Unit tests for scripts/parse_existing_runs.py

Tests the QoR metrics parser: slack computation, JSON/RPT parsing,
path handling, confidence scoring, and multi-corner extraction.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from parse_existing_runs import (
    compute_setup_slack,
    compute_hold_slack,
    parse_metrics_json,
    parse_summary_rpt,
    _to_relative_posix,
    _extract_corners,
    _find_worst_corners,
    compute_confidence,
    ROOT,
)


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
    "timing__setup__ws__corner:nom_ss_100C_1v60": 13.435060555451257,
    "timing__setup__wns__corner:nom_ss_100C_1v60": 0,
    "timing__hold__ws__corner:nom_ss_100C_1v60": 0.9427198177813103,
    "timing__hold__wns__corner:nom_ss_100C_1v60": 0,
    "design__max_slew_violation__count__corner:nom_ss_100C_1v60": 212,
}

SAMPLE_FAILING_JSON = {
    "timing__setup__wns": -1.5,
    "timing__setup__ws": -1.5,
    "timing__setup__tns": -25.3,
    "timing__hold__wns": 0,
    "timing__hold__ws": 0.05,
    "timing__hold__tns": 0,
    "design__core__area": 15942.8,
    "design__instance__utilization": 0.85,
    "power__total": 0.002,
    "power__internal__total": 0.001,
    "power__switching__total": 0.0008,
    "power__leakage__total": 2e-08,
    "design__max_slew_violation__count": 500,
    "design__max_cap_violation__count": 10,
    "design__max_fanout_violation__count": 3,
    "route__drc_errors": 5,
    "route__wirelength": 40000,
    "route__vias": 8000,
    "global_route__wirelength": 55000,
    "global_route__vias": 20,
    "antenna__violating__nets": 2,
}


class TestComputeSetupSlack(unittest.TestCase):
    """Tests for compute_setup_slack normalization logic."""

    def test_compute_setup_slack_negative_wns(self):
        """WNS < 0 → returns WNS (timing failure)."""
        result = compute_setup_slack(-2.5, 0)
        self.assertEqual(result, -2.5)

    def test_compute_setup_slack_zero_wns_with_ws(self):
        """WNS = 0, WS > 0 → returns WS (positive margin)."""
        result = compute_setup_slack(0, 13.237)
        self.assertEqual(result, 13.237)

    def test_compute_setup_slack_only_ws(self):
        """WNS missing, WS available → returns WS."""
        result = compute_setup_slack(None, 5.0)
        self.assertEqual(result, 5.0)

    def test_compute_setup_slack_only_wns_zero(self):
        """WNS = 0, WS missing → returns 0."""
        result = compute_setup_slack(0, None)
        self.assertEqual(result, 0)

    def test_compute_setup_slack_all_missing(self):
        """Both None → returns None."""
        result = compute_setup_slack(None, None)
        self.assertIsNone(result)


class TestComputeHoldSlack(unittest.TestCase):
    """Tests for compute_hold_slack normalization logic."""

    def test_compute_hold_slack_negative(self):
        """Hold WNS < 0 → returns WNS (hold failure)."""
        result = compute_hold_slack(-0.3, 0)
        self.assertEqual(result, -0.3)

    def test_compute_hold_slack_positive_ws(self):
        """WNS = 0, WS > 0 → returns WS."""
        result = compute_hold_slack(0, 0.119)
        self.assertEqual(result, 0.119)


class TestParseMetricsJson(unittest.TestCase):
    """Tests for parse_metrics_json extraction logic."""

    def test_parse_metrics_json_clean_timing(self):
        """JSON with WNS=0, WS=13.237 → verify separate fields."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(SAMPLE_METRICS_JSON, f)
            f.flush()
            path = Path(f.name)

        try:
            result = parse_metrics_json(path)
            # WNS and WS stored separately
            self.assertEqual(result["setup_wns"], "0")
            self.assertIn("13.237", result["setup_ws"])
            # Normalized slack should be WS since WNS=0
            self.assertIn("13.237", result["setup_slack"])
            # Hold
            self.assertEqual(result["hold_wns"], "0")
            self.assertIn("0.119", result["hold_ws"])
        finally:
            path.unlink()

    def test_parse_metrics_json_failing_timing(self):
        """WNS=-1.5, WS=-1.5 → verify negative slack."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(SAMPLE_FAILING_JSON, f)
            f.flush()
            path = Path(f.name)

        try:
            result = parse_metrics_json(path)
            self.assertEqual(result["setup_wns"], "-1.5")
            # Normalized slack should be WNS since it's negative
            self.assertEqual(result["setup_slack"], "-1.5")
        finally:
            path.unlink()

    def test_parse_metrics_json_malformed(self):
        """Invalid JSON → returns warning."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            f.write("{ invalid json content !!!")
            f.flush()
            path = Path(f.name)

        try:
            result = parse_metrics_json(path)
            self.assertIn("_parser_warnings", result)
            self.assertTrue(
                any("Malformed" in w for w in result["_parser_warnings"])
            )
        finally:
            path.unlink()

    def test_parse_metrics_json_empty(self):
        """Empty file → returns empty dict."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            f.write("")
            f.flush()
            path = Path(f.name)

        try:
            result = parse_metrics_json(path)
            self.assertEqual(result, {})
        finally:
            path.unlink()

    def test_parse_metrics_json_multi_corner(self):
        """JSON with corner-specific keys → corners extracted."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(SAMPLE_METRICS_JSON, f)
            f.flush()
            path = Path(f.name)

        try:
            result = parse_metrics_json(path)
            # Should have corner data
            self.assertIn("_corners", result)
            corners = result["_corners"]
            self.assertIn("nom_ss_100C_1v60", corners)
            self.assertEqual(result["timing_corners_count"], "1")
            self.assertEqual(result["worst_setup_corner"], "nom_ss_100C_1v60")
        finally:
            path.unlink()


class TestParseSummaryRpt(unittest.TestCase):
    """Tests for parse_summary_rpt extraction logic."""

    def test_parse_summary_rpt_valid(self):
        """Valid .rpt file with Overall row → parses correctly."""
        rpt_content = (
            "Corner  Hold Slack  Hold Reg2Reg  Hold TNS  Hold Vio  Hold r2r  "
            "Setup Slack  Setup Reg2Reg  Setup TNS  Setup Vio  Setup r2r  Max Cap  Max Slew\n"
            "-----------------------------------------------------------------------\n"
            "Overall  0.119  0.200  0  0  0  13.237  14.0  0  0  0  0  247\n"
        )
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".rpt", delete=False
        ) as f:
            f.write(rpt_content)
            f.flush()
            path = Path(f.name)

        try:
            result = parse_summary_rpt(path)
            self.assertIn("hold_ws", result)
            self.assertIn("setup_ws", result)
        finally:
            path.unlink()

    def test_parse_summary_rpt_empty(self):
        """Empty file → returns empty dict."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".rpt", delete=False
        ) as f:
            f.write("")
            f.flush()
            path = Path(f.name)

        try:
            result = parse_summary_rpt(path)
            self.assertEqual(result, {})
        finally:
            path.unlink()


class TestToRelativePosix(unittest.TestCase):
    """Tests for _to_relative_posix path conversion."""

    def test_to_relative_posix(self):
        """Converts absolute path under ROOT to relative POSIX."""
        test_path = ROOT / "results" / "important_reports" / "metrics.json"
        result = _to_relative_posix(test_path)
        self.assertIn("results/important_reports/metrics.json", result)
        # Should not contain backslashes (POSIX)
        self.assertNotIn("\\", result)


class TestConfidence(unittest.TestCase):
    """Tests for compute_confidence scoring logic."""

    def test_confidence_high(self):
        """JSON source, timing available, no errors → 'high'."""
        metrics = {
            "setup_wns": "0",
            "setup_ws": "13.237",
            "setup_tns": "0",
            "hold_wns": "0",
            "hold_ws": "0.119",
            "hold_tns": "0",
            "setup_slack": "13.237",
            "area": "15942.8",
            "utilization": "0.85",
            "power_total": "0.00134",
            "power_internal": "0.0009",
            "power_switching": "0.0004",
            "power_leakage": "1.9e-08",
            "slew_violations": "247",
            "cap_violations": "0",
            "fanout_violations": "1",
            "route_drc_errors": "0",
            "route_wirelength": "37368",
            "route_vias": "7603",
            "grt_wirelength": "53219",
            "grt_vias": "15",
            "antenna_violations": "0",
        }
        field_sources = {"setup_wns": "json:timing__setup__wns"}
        warnings = []
        result = compute_confidence(metrics, field_sources, warnings)
        self.assertEqual(result, "high")

    def test_confidence_none(self):
        """No metrics → 'none'."""
        metrics = {}
        field_sources = {}
        warnings = []
        result = compute_confidence(metrics, field_sources, warnings)
        self.assertEqual(result, "none")


class TestExtractCorners(unittest.TestCase):
    """Tests for _extract_corners multi-corner parsing."""

    def test_extract_corners(self):
        """Extracts corner data correctly from flat JSON keys."""
        corners = _extract_corners(SAMPLE_METRICS_JSON)
        self.assertIn("nom_ss_100C_1v60", corners)
        corner = corners["nom_ss_100C_1v60"]
        self.assertIn("setup_ws", corner)
        self.assertAlmostEqual(corner["setup_ws"], 13.435060555451257)
        self.assertEqual(corner["setup_wns"], 0)
        self.assertIn("hold_ws", corner)
        self.assertAlmostEqual(corner["hold_ws"], 0.9427198177813103)
        self.assertIn("slew_violations", corner)
        self.assertEqual(corner["slew_violations"], 212)


class TestFindWorstCorners(unittest.TestCase):
    """Tests for _find_worst_corners logic."""

    def test_find_worst_corners(self):
        """Identifies worst corners from multi-corner data."""
        corners = {
            "corner_a": {"setup_wns": 0, "setup_ws": 5.0, "hold_wns": 0, "hold_ws": 1.0},
            "corner_b": {"setup_wns": 0, "setup_ws": 3.0, "hold_wns": 0, "hold_ws": 0.5},
            "corner_c": {"setup_wns": -1.0, "setup_ws": -1.0, "hold_wns": 0, "hold_ws": 2.0},
        }
        worst_setup, worst_hold = _find_worst_corners(corners)
        # corner_c has negative setup slack = -1.0 (worst)
        self.assertEqual(worst_setup, "corner_c")
        # corner_b has smallest hold slack = 0.5
        self.assertEqual(worst_hold, "corner_b")


if __name__ == "__main__":
    unittest.main()
